import re
import unittest
from html.parser import HTMLParser

from engine import alerts, digest

NOW = 1_794_700_000
HOSTILE = '<script>alert(1)</script> & "quotes" <b>bold</b>'


class Tags(HTMLParser):
    """Collects the tags in a message and checks they nest properly."""

    def __init__(self):
        super().__init__()
        self.stack, self.seen, self.bad = [], set(), False

    def handle_starttag(self, tag, attrs):
        self.seen.add(tag)
        self.stack.append(tag)
        if tag == "a" and not any(k == "href" and v.startswith("https://") for k, v in attrs):
            self.bad = True

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad = True


def every_kind(n=1):
    base = {"symbol": f"S{n}.NS", "name": f"Name {n} {HOSTILE}", "currency": "INR", "price": 100.0 + n}
    return [
        {**base, "kind": "signal", "horizon": "short", "side": "buy", "announced": "hold", "label": "Buy", "score": 30, "reasons": [HOSTILE], "levels": None},
        {**base, "kind": "deal", "qty": 1_000_000, "value": 2e8, "window": "Block deal 1", "time": "09:05", "pchange": 1.2, "previous_close": 99.0, "official_url": ""},
        {**base, "kind": "ipo", "tags": ["IPO breakout: above listing-day high 120.00"], "move": 3.0},
        {**base, "kind": "mover", "tags": ["+9.0%", HOSTILE], "move": 9.0, "small": True},
        {**base, "kind": "low52", "tags": ["at 52-week low"], "move": -2.0},
        {"kind": "index", "symbol": "", "name": "NIFTY 50", "tags": ["+1.1%", "at 52-week high"], "last": 25000.5},
        {"kind": "brief", "symbol": "", "name": "India pre-open brief", "window": "in_preopen", "lines": ["Pre-open indicative: up A +3.0%", HOSTILE], "ts": NOW},
        {**base, "kind": "news", "title": HOSTILE, "source": "Site.com", "link": "https://news.example/a?x=1&y=2", "age_min": 30},
    ]


class AllKindsTests(unittest.TestCase):
    def messages(self, items, links=None):
        return digest.render(items, links if links is not None else [f"https://site/#stock=S{i}.NS" for i in range(len(items))], NOW)

    def test_every_kind_renders_in_its_own_section_with_valid_markup(self):
        text = self.messages(every_kind())[0][0]
        for title in (digest.READINGS_TITLE, digest.DEALS_TITLE, digest.IPO_TITLE, digest.MOVERS_TITLE, digest.LOW_TITLE, digest.INDEX_TITLE, digest.BRIEF_TITLE, digest.NEWS_TITLE):
            self.assertIn(title, text, title)
        parser = Tags()
        parser.feed(text)
        self.assertFalse(parser.bad, "tags must nest and every link must be https")
        self.assertLessEqual(parser.seen, {"b", "a"}, "only bold and links are used")

    def test_text_from_outside_never_becomes_markup(self):
        text = self.messages(every_kind())[0][0]
        self.assertNotIn("<script", text)
        self.assertNotIn("<b>bold</b>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_messages_stay_under_the_telegram_limit_even_with_many_alerts(self):
        items = [item for n in range(12) for item in every_kind(n)]
        messages = self.messages(items)
        self.assertLessEqual(len(messages), digest.MAX_MESSAGES)
        for text, _, _ in messages:
            self.assertLessEqual(alerts.visible_length(text), 4096, "Telegram rejects a longer message")
            self.assertTrue(text.endswith(alerts.ADVISORY))
        covered = sum(end - start for _, start, end in messages)
        self.assertEqual(messages[0][1], 0)
        self.assertTrue(all(a[2] == b[1] for a, b in zip(messages, messages[1:])), "messages cover the alerts in order, with no gaps")
        if covered < len(items):
            self.assertIn("more alert", messages[-1][0])

    def test_the_header_counts_add_up_to_the_alerts_in_the_message(self):
        items = every_kind()
        text = self.messages(items)[0][0]
        header = text.split("\n")[1]
        numbers = [int(n) for n in re.findall(r"(\d+) (?:reading|block|mover|market|headline)", header)]
        self.assertEqual(sum(numbers), len(items))

    def test_the_plain_text_fallback_has_no_markup_left(self):
        plain = alerts.to_plain(self.messages(every_kind())[0][0]) if hasattr(alerts, "to_plain") else digest.to_plain(self.messages(every_kind())[0][0])
        self.assertNotIn("<a href", plain)
        self.assertNotIn("<b>Name", plain)

    def test_alerts_without_a_link_still_render(self):
        text = self.messages(every_kind(), links=[None] * 8)[0][0]
        self.assertNotIn("Open analysis", text)


if __name__ == "__main__":
    unittest.main()
