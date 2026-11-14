"""Glue between a build run, the track-record ledger and Telegram.

A call is recorded when a Buy or Sell alert goes out. Later runs check open calls against new prices, and when one
reaches its target or its stop-loss a reply is sent under the original Telegram message.

Nothing here may stop the site from publishing: callers treat every failure as "try again next run".
"""
from . import alerts, ledger

PENDING_LIMIT = 500
REPLY_LIMIT = 20
QUEUE_LIMIT = 100
NOTIFIED_LIMIT = 500
REPLY_KINDS = ("target", "stop")


def make_calls(items, records, now_ts):
    """[(line index, call)] for the Buy/Sell moves among the alert items."""
    found = []
    for index, item in enumerate(items):
        record = records.get(item["symbol"])
        if item["kind"] != "signal" or not record:
            continue
        call = ledger.make_call(record, item["symbol"], item["horizon"], item["side"], item["announced"], now_ts)
        if call:
            found.append((index, call))
    return found


def attach_message(calls, shown, message_id):
    """Remember which Telegram message announced each call, for those that were in the sent digest."""
    if not message_id:
        return
    for index, call in calls:
        if index < shown:
            call["telegram_message_id"] = message_id


def send_replies(queue, led, notified, send, link_for=None, limit=REPLY_LIMIT):
    """Reply to the announcement of each call that reached its target or stop. send(text, reply_to) -> bool.

    Returns (sent ids, ids to retry next run, texts sent). Ids already notified, or whose outcome is neither
    target nor stop (an expiry is not announced), are dropped.
    """
    sent, remaining, texts = [], [], []
    done = set(notified)
    for oid in queue:
        outcome, call = led.outcomes.get(oid), led.calls.get(oid)
        if oid in done or not outcome or not call or outcome["outcome"] not in REPLY_KINDS:
            continue
        if len(sent) >= limit:
            remaining.append(oid)
            continue
        text = alerts.format_outcome(call, outcome, link_for(call) if link_for else None)
        if send(text, call.get("telegram_message_id")):
            sent.append(oid)
            texts.append(alerts.format_outcome(call, outcome))
            done.add(oid)
        else:
            remaining.append(oid)
    return sent, remaining[:QUEUE_LIMIT], texts


def open_ledger(directory, remote=None, token=None):
    """A loaded Ledger, with the data branch cloned into `directory` first if a remote is given.
    Returns None (after saying why) if the ledger cannot be reached."""
    from . import ledger_git
    try:
        if remote:
            ledger_git.prepare(directory, remote, token=token)
        return ledger.Ledger(directory).load()
    except (ledger_git.GitError, OSError) as err:
        print("Ledger unavailable, will retry next run:", err)
        return None


def publish_ledger(directory, message, remote, token=None):
    """Push the ledger. True if it is safely stored (or there is no remote to store it in)."""
    if not remote:
        return True
    from . import ledger_git
    try:
        ledger_git.publish(directory, message, token=token)
        return True
    except (ledger_git.GitError, OSError) as err:
        print("Ledger not saved, will retry next run:", err)
        return False
