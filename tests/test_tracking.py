import argparse
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import alerts, build, ledger, tracking
from tests.test_ledger import call, day, make_bars


def record(price=100.0):
    return {"name": "Acme", "market": "US", "sector": "Tech", "price": price, "currency": "USD",
            "indicators": {"atr": 2.0, "hi252": 130.0, "lo252": 70.0},
            "levels": {"zones": {"resistance": [{"level": 108.0, "touches": 2}], "support": [{"level": 92.0, "touches": 2}]}},
            "short": {"label": "Buy", "score": 40, "reasons": []}, "long": {"label": "Hold", "score": 5, "reasons": []}}


def item(symbol="AAA", kind="signal", horizon="short", side="buy"):
    return {"symbol": symbol, "kind": kind, "horizon": horizon, "side": side, "announced": "hold"}


class MakeCallsTests(unittest.TestCase):
    def test_only_signal_moves_into_buy_or_sell_become_calls(self):
        items = [item(kind="news"), item(side="hold"), item(symbol="ZZZ"), item()]
        found = tracking.make_calls(items, {"AAA": record()}, 1_793_000_000)
        self.assertEqual([index for index, _ in found], [3])

    def test_the_message_id_goes_only_to_calls_that_were_in_the_digest(self):
        calls = [(0, {"id": "a"}), (5, {"id": "b"})]
        tracking.attach_message(calls, 3, 77)
        self.assertEqual(calls[0][1].get("telegram_message_id"), 77)
        self.assertNotIn("telegram_message_id", calls[1][1])

    def test_no_message_id_leaves_calls_untouched(self):
        calls = [(0, {"id": "a"})]
        tracking.attach_message(calls, 3, None)
        self.assertNotIn("telegram_message_id", calls[0][1])


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.led = ledger.Ledger(self.dir)
        self.hit = call(telegram_message_id=11)
        self.led.add_call(self.hit)
        self.led.add_outcome({"type": "outcome", "id": self.hit["id"], "resolved_date": day(3), "outcome": "target",
                              "exit_price": 108.0, "return_pct": 8.0, "days_held": 3})

    def test_a_target_hit_is_a_reply_to_the_original_message(self):
        sent = []
        ids, left, texts = tracking.send_replies([self.hit["id"]], self.led, [], lambda t, r: sent.append((t, r)) or True,
                                                 lambda c: "https://x/#stock=AAA")
        self.assertEqual(ids, [self.hit["id"]])
        self.assertEqual(left, [])
        self.assertEqual(sent[0][1], 11)
        self.assertTrue(sent[0][0].startswith("REFERENCE LEVEL REACHED: Aaa Ltd (AAA)"))
        self.assertIn("Full analysis: https://x/#stock=AAA", sent[0][0])
        self.assertNotIn("https", texts[0])

    def test_a_failed_send_is_retried_and_a_sent_one_is_not_repeated(self):
        ids, left, _ = tracking.send_replies([self.hit["id"]], self.led, [], lambda t, r: False)
        self.assertEqual((ids, left), ([], [self.hit["id"]]))
        again = []
        ids, left, _ = tracking.send_replies([self.hit["id"]], self.led, [self.hit["id"]], lambda t, r: again.append(1) or True)
        self.assertEqual((ids, left, again), ([], [], []))

    def test_an_expiry_is_not_announced(self):
        other = call(price=90.0)
        self.led.add_call(other)
        self.led.add_outcome({"type": "outcome", "id": other["id"], "resolved_date": day(9), "outcome": "expired",
                              "exit_price": 91.0, "return_pct": 1.0, "days_held": 5})
        sent = []
        ids, left, _ = tracking.send_replies([other["id"]], self.led, [], lambda t, r: sent.append(t) or True)
        self.assertEqual((ids, left, sent), ([], [], []))

    def test_the_stop_wording_and_a_call_without_a_message_id(self):
        plain = call(side="sell", price=100.0)
        self.led.add_call(plain)
        self.led.add_outcome({"type": "outcome", "id": plain["id"], "resolved_date": day(2), "outcome": "stop",
                              "exit_price": 104.0, "return_pct": -4.0, "days_held": 2})
        sent = []
        tracking.send_replies([plain["id"]], self.led, [], lambda t, r: sent.append((t, r)) or True)
        self.assertTrue(sent[0][0].startswith("VOLATILITY RISK LEVEL REACHED"))
        self.assertIsNone(sent[0][1])

    def test_a_burst_is_capped_and_the_rest_waits(self):
        ids = []
        for n in range(5):
            c = call(price=100.0 + n + 1)
            self.led.add_call(c)
            self.led.add_outcome({"type": "outcome", "id": c["id"], "resolved_date": day(3), "outcome": "target",
                                  "exit_price": 120.0, "return_pct": 8.0, "days_held": 3})
            ids.append(c["id"])
        sent, left, _ = tracking.send_replies(ids, self.led, [], lambda t, r: True, limit=2)
        self.assertEqual((len(sent), len(left)), (2, 3))


class PostTelegramTests(unittest.TestCase):
    def fake(self, body, status=200):
        response = mock.MagicMock()
        response.status = status
        response.read.return_value = body
        response.__enter__.return_value = response
        return response

    def test_it_returns_the_message_id_and_threads_replies(self):
        with mock.patch("urllib.request.urlopen", return_value=self.fake(b'{"ok":true,"result":{"message_id":42}}')) as opened:
            self.assertEqual(alerts.post_telegram("T", "C", "hi", reply_to=7), (True, 42))
        payload = json.loads(opened.call_args[0][0].data)
        self.assertEqual((payload["reply_to_message_id"], payload["allow_sending_without_reply"]), (7, True))

    def test_a_plain_message_has_no_reply_fields_and_a_bad_body_is_still_ok(self):
        with mock.patch("urllib.request.urlopen", return_value=self.fake(b"not json")) as opened:
            self.assertEqual(alerts.post_telegram("T", "C", "hi"), (True, None))
        self.assertNotIn("reply_to_message_id", json.loads(opened.call_args[0][0].data))

    def test_a_network_error_is_a_failure_not_a_crash(self):
        import urllib.error
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            self.assertEqual(alerts.post_telegram("T", "C", "hi"), (False, None))

    def test_a_boolean_message_id_is_not_accepted(self):
        with mock.patch("urllib.request.urlopen", return_value=self.fake(b'{"result":{"message_id":true}}')):
            self.assertEqual(alerts.post_telegram("T", "C", "hi"), (True, None))


class TrackStepTests(unittest.TestCase):
    """build.track with a local ledger folder: the whole cycle, apart from Telegram's network."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()) / "ledger"
        self.addCleanup(shutil.rmtree, self.dir.parent, True)
        self.args = argparse.Namespace(ledger_dir=str(self.dir), ledger_remote="")
        self.env = mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_track(self, state, calls, bars, token="T", post=None, now_ts=1_793_000_000):
        ctx = {"bars": bars, "bench": {}}
        post = post or (lambda *a, **k: (True, 900))
        with mock.patch.object(alerts, "post_telegram", post):
            return build.track(self.args, state, {}, calls, ctx, now_ts, token, "C" if token else None, lambda s, section="signals": f"https://s/#stock={s}")

    def test_a_call_is_stored_then_resolved_and_replied_to_once(self):
        c = call(telegram_message_id=11)
        summary, pending, queue, notified, lines = self.run_track({}, [(0, c)], {})
        self.assertEqual((pending, queue, lines), ([], [], []))
        self.assertEqual(summary["open"], 1)
        self.assertTrue(list(self.dir.glob("calls-*.jsonl")))

        replies = []
        summary, pending, queue, notified, lines = self.run_track(
            {"notified_outcomes": notified}, [], {"AAA": make_bars([(100, 109, 99, 108)])},
            post=lambda t, c_, text, reply_to=None: (replies.append((text, reply_to)), (True, 1))[1])
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0][1], 11)
        self.assertEqual(lines[0][0], "AAA")
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(len(notified), 1)

        again = []
        self.run_track({"notified_outcomes": notified}, [], {"AAA": make_bars([(100, 109, 99, 108)])},
                       post=lambda t, c_, text, reply_to=None: again.append(1) or (True, 1))
        self.assertEqual(again, [], "an outcome already announced is never announced twice")

    def test_without_telegram_the_ledger_still_works_and_no_replies_are_queued(self):
        c = call()
        self.run_track({}, [(0, c)], {}, token=None)
        summary, pending, queue, notified, lines = self.run_track(
            {}, [], {"AAA": make_bars([(100, 109, 99, 108)])}, token=None)
        self.assertEqual((queue, notified, lines), ([], [], []))
        self.assertEqual(summary["resolved"], 1)

    def test_calls_wait_in_state_when_the_ledger_cannot_be_reached(self):
        c = call()
        with mock.patch.object(tracking, "open_ledger", return_value=None):
            summary, pending, *_ = self.run_track({"track_record": None}, [(0, c)], {})
        self.assertEqual([p["id"] for p in pending], [c["id"]])

    def test_pending_calls_are_saved_once_the_ledger_is_back_and_not_twice(self):
        c = call()
        summary, pending, *_ = self.run_track({"ledger_pending": [c]}, [(0, c)], {})
        self.assertEqual(pending, [])
        self.assertEqual(summary["calls_total"], 1)
        lines = sum(len(p.read_text().splitlines()) for p in self.dir.glob("calls-*.jsonl"))
        self.assertEqual(lines, 1)

    def test_a_failed_publish_keeps_calls_pending_and_the_site_still_builds(self):
        c = call()
        with mock.patch.object(tracking, "publish_ledger", return_value=False):
            summary, pending, *_ = self.run_track({}, [(0, c)], {})
        self.assertEqual(len(pending), 1)
        self.assertEqual(summary["calls_total"], 1)

    def test_disabled_when_no_ledger_is_configured(self):
        self.args = argparse.Namespace(ledger_dir="", ledger_remote="")
        out = build.track(self.args, {"ledger_pending": []}, {"track_record": {"x": 1}}, [], {"bars": {}, "bench": {}}, 1, None, None, lambda s: None)
        self.assertEqual(out[0], {"x": 1})

    def test_garbage_in_state_is_ignored(self):
        summary, pending, queue, notified, _ = self.run_track(
            {"ledger_pending": ["x", {"nope": 1}, None], "reply_queue": [1, None], "notified_outcomes": [3]}, [], {})
        self.assertEqual((pending, queue, notified), ([], [], []))


if __name__ == "__main__":
    unittest.main()
