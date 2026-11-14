import unittest
from datetime import date, datetime, timezone

from engine import ipo_gmp
from engine.fetch import FetchError

HEADER = ("<tr><th>Name &#9650; &#9660;</th><th>GMP &#9650;</th><th>&#128293;Rating &#9650;</th><th>Sub</th><th>Price (&#8377;) &#9650;</th>"
          "<th>IPO Size &#9650;</th><th>Lot</th><th>Open</th><th>Close</th><th>BoA Dt &#9650;</th><th>Listing</th>"
          "<th>Updated-On &#9650;</th><th>Anchor &#9650;</th></tr>")


def cell(inner):
    return f'<td><div class="report-td "><div class="mono-num">{inner}</div></div></td>'


def row(name_html, gmp="&#8377;<b>16</b> (6.53%)<br><small><b>16 &darr; / 16 &uarr;</b></small>", rating="&#128293;&#128293;", sub="-",
        price="245", size="&#8377;120.10 Cr", lot="600", open_="19-Nov", close="23-Nov", boa="25-Nov", listing="27-Nov",
        updated="14-Nov 8:02", anchor="&#9989;"):
    cells = [name_html, gmp, rating, sub, price, size, lot, open_, close, boa, listing, updated, anchor]
    return "<tr>" + "".join(cell(c) for c in cells) + "</tr>"


UPCOMING = ('<a href="/gmp/watrana-rentals-ipo/1818/" title="Watrana Rentals" target="_parent">Watrana Rentals</a> '
            '<span class="badge rounded-pill bg-secondary d-inline ms-2">NSE SME</span>'
            '<span class="badge rounded-pill bg-warning d-inline ms-2">U</span>')
LISTED = ('<a href="/gmp/antala-industries-ipo/2375/" title="Antala Industries" target="_parent">Antala Industries</a> '
          '<span class="badge rounded-pill bg-secondary d-inline ms-2">BSE SME</span>'
          '<span class=\'text-danger d-inline ms-2\'><small><b><a href="/cdn-cgi/l/email-protection" class="__cf_email__" '
          'data-cfemail="6a262a5f5844525a">[email&#160;protected]</a> (-20%)</b></small></span>')
MAINBOARD = ('<a href="/gmp/big-co-ipo/1/" title="Big Co &amp; Sons" target="_parent">Big Co &amp; Sons</a> '
             '<span class="badge rounded-pill bg-secondary d-inline ms-2">NSE</span>')


def page(*rows):
    return f"<html><body><table>{HEADER}{''.join(rows)}</table></body></html>"


TODAY = date(2026, 11, 14)


class DecodeTests(unittest.TestCase):
    def test_cloudflare_email_decode(self):
        self.assertEqual(ipo_gmp.decode_cloudflare_email("6a262a5f5844525a"), "L@52.80")

    def test_bad_hex(self):
        self.assertIsNone(ipo_gmp.decode_cloudflare_email("zz"))
        self.assertIsNone(ipo_gmp.decode_cloudflare_email(""))


class ParseTests(unittest.TestCase):
    def test_upcoming_row(self):
        rows = ipo_gmp.parse_table(page(row(UPCOMING)), TODAY)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["name"], "Watrana Rentals")
        self.assertEqual(r["board"], "SME")
        self.assertEqual(r["exchange"], "NSE")
        self.assertEqual(r["gmp"], 16.0)
        self.assertEqual(r["gmp_pct"], 6.53)
        self.assertEqual(r["price"], 245.0)
        self.assertEqual(r["lot"], 600)
        self.assertEqual(r["size_cr"], 120.10)
        self.assertEqual((r["open"], r["close"], r["allotment"], r["listing"]), ("2026-11-19", "2026-11-23", "2026-11-25", "2026-11-27"))
        self.assertEqual(r["rating"], 2)
        self.assertTrue(r["anchor"])
        self.assertIsNone(r["subscription"])
        self.assertIsNone(r["listed_price"])
        self.assertTrue(r["link"].startswith("https://www.investorgain.com/gmp/"))

    def test_listed_row_decodes_listing_result(self):
        rows = ipo_gmp.parse_table(page(row(LISTED, gmp="&#8377;<b>--</b> (0.00%)", sub="12.79x", price="66", lot="2,000",
                                             open_="29-Oct", close="5-Nov", boa="6-Nov", listing="11-Nov GMP: 0", anchor="&#10060;")), TODAY)
        r = rows[0]
        self.assertEqual(r["listed_price"], 52.8)
        self.assertEqual(r["listed_gain_pct"], -20.0)
        self.assertIsNone(r["gmp"])
        self.assertIsNone(r["gmp_pct"])
        self.assertEqual(r["subscription"], 12.79)
        self.assertEqual(r["lot"], 2000)
        self.assertEqual(r["listing"], "2026-11-11")
        self.assertIs(r["anchor"], False)

    def test_mainboard_and_entities(self):
        r = ipo_gmp.parse_table(page(row(MAINBOARD)), TODAY)[0]
        self.assertEqual(r["name"], "Big Co & Sons")
        self.assertEqual(r["board"], "Mainboard")

    def test_negative_gmp(self):
        r = ipo_gmp.parse_table(page(row(UPCOMING, gmp="&#8377;<b>-5</b> (-2.04%)")), TODAY)[0]
        self.assertEqual(r["gmp"], -5.0)
        self.assertEqual(r["gmp_pct"], -2.04)

    def test_dates_with_gmp_history_text(self):
        r = ipo_gmp.parse_table(page(row(UPCOMING, open_="2-Nov<br><small><b>GMP: 15</b></small>", close="4-Nov<br><small><b>GMP: 1</b></small>",
                                         boa="5-Nov", listing="9-Nov<br><small><b>GMP: 1</b></small>")), TODAY)[0]
        self.assertEqual((r["open"], r["listing"]), ("2026-11-02", "2026-11-09"))

    def test_year_rollover(self):
        today = date(2026, 12, 28)
        r = ipo_gmp.parse_table(page(row(UPCOMING, open_="29-Dec", close="31-Dec", boa="2-Jan", listing="5-Jan")), today)[0]
        self.assertEqual((r["open"], r["close"], r["allotment"], r["listing"]), ("2026-12-29", "2026-12-31", "2027-01-02", "2027-01-05"))

    def test_bad_rows_are_skipped_not_guessed(self):
        good = row(UPCOMING)
        rows = ipo_gmp.parse_table(page(good, row(UPCOMING, price="--"), row(UPCOMING, lot="0"), row(UPCOMING, open_="soon"),
                                        row(UPCOMING, listing="10-Jan"), row("no link here")), TODAY)
        self.assertEqual(len(rows), 1)

    def test_layout_change_returns_none(self):
        self.assertIsNone(ipo_gmp.parse_table("<html>no table</html>", TODAY))
        self.assertIsNone(ipo_gmp.parse_table(page(), TODAY))
        changed = page(row(UPCOMING)).replace("<th>Lot</th>", "<th>Shares</th>")
        self.assertIsNone(ipo_gmp.parse_table(changed, TODAY))
        self.assertIsNone(ipo_gmp.parse_table("<table><tr><td>x</td></tr></table>", TODAY))


class NewsGmpTests(unittest.TestCase):
    def test_extracts_explicit_rupee_figures(self):
        ex = ipo_gmp.extract_gmp
        self.assertEqual(ex("Watrana Rentals IPO GMP today: ₹40 premium, check price band")["rupees"], 40.0)
        self.assertEqual(ex("Crystal Crop IPO GMP at Rs 40 ahead of listing")["rupees"], 40.0)
        self.assertEqual(ex("XYZ IPO GMP is Rs. 12.5 per share")["rupees"], 12.5)
        self.assertEqual(ex("ABC IPO grey market premium of INR 30")["rupees"], 30.0)
        self.assertEqual(ex("ABC IPO GMP falls to -₹5 as subscription lags")["rupees"], -5.0)
        self.assertEqual(ex("ABC IPO GMP -Rs 5")["rupees"], -5.0)

    def test_extracts_percent(self):
        found = ipo_gmp.extract_gmp("ABC IPO GMP signals 25% listing gain")
        self.assertEqual(found["percent"], 25.0)
        self.assertIsNone(found["rupees"])

    def test_never_reads_a_date_or_year_as_a_premium(self):
        ex = ipo_gmp.extract_gmp
        self.assertIsNone(ex("ABC IPO GMP today (Nov 14): check details"))
        self.assertIsNone(ex("ABC IPO GMP 2026: price band, lot size"))
        self.assertIsNone(ex("ABC IPO GMP: 40"))
        self.assertIsNone(ex("ABC IPO opens Nov 18, price band Rs 95-100"))
        self.assertIsNone(ex("Sensex falls 300 points; premium of Rs 40 seen"))
        self.assertIsNone(ex(""))
        self.assertIsNone(ex(None))

    def test_agreement(self):
        self.assertTrue(ipo_gmp.gmp_agrees(40, 42))
        self.assertTrue(ipo_gmp.gmp_agrees(100, 120))
        self.assertFalse(ipo_gmp.gmp_agrees(16, 30))
        self.assertFalse(ipo_gmp.gmp_agrees(100, 160))
        self.assertTrue(ipo_gmp.gmp_agrees(0, 3))
        self.assertIsNone(ipo_gmp.gmp_agrees(None, 30))
        self.assertIsNone(ipo_gmp.gmp_agrees(30, None))

    def rows(self):
        base = {"name": "Watrana Rentals", "gmp": 16.0, "listing": "2026-11-27"}
        return [dict(base), dict(base, name="Old Listed Co", listing="2026-11-01")]

    def test_enrich_adds_cross_check_for_active_ipos_only(self):
        now = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
        asked = []

        def fake_news(query, market, when, limit=10):
            asked.append(query)
            return [
                {"title": "Sensex jumps; IPO GMP ₹999 hype", "link": "", "age_min": 30},
                {"title": "Watrana Rentals IPO GMP today: ₹30 premium", "link": "https://example.com/a", "age_min": 90},
            ]

        rows = ipo_gmp.enrich_with_news(self.rows(), now, fake_news)
        self.assertEqual(asked, ['"Watrana Rentals" IPO GMP'], "listed IPOs must not be searched")
        self.assertEqual(rows[0]["news_gmp"]["rupees"], 30.0)
        self.assertIs(rows[0]["news_gmp"]["agrees"], False)
        self.assertEqual(rows[0]["news_gmp"]["link"], "https://example.com/a")
        self.assertIsNone(rows[1]["news_gmp"])

    def test_percent_only_headline_is_compared_with_the_source_percent(self):
        now = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
        row = {"name": "Crystal Crop Protection", "gmp": 40.0, "gmp_pct": 14.71, "listing": "2026-11-26"}
        title = "Crystal Crop Protection IPO: Price band set, GMP signals 16.5% listing gain"
        close = ipo_gmp.enrich_with_news([dict(row)], now, lambda *a, **k: [{"title": title, "link": "", "age_min": 60}])[0]
        self.assertEqual(close["news_gmp"]["percent"], 16.5)
        self.assertIs(close["news_gmp"]["agrees"], True)
        far = ipo_gmp.enrich_with_news([dict(row, gmp_pct=3.0)], now, lambda *a, **k: [{"title": title, "link": "", "age_min": 60}])[0]
        self.assertIs(far["news_gmp"]["agrees"], False)

    def test_old_or_unrelated_headlines_are_ignored(self):
        now = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)

        def fake_news(query, market, when, limit=10):
            return [{"title": "Watrana Rentals IPO GMP today: ₹30", "link": "", "age_min": 60 * 100},
                    {"title": "Another Firm IPO GMP today: ₹30", "link": "", "age_min": 10}]

        self.assertIsNone(ipo_gmp.enrich_with_news(self.rows(), now, fake_news)[0]["news_gmp"])

    def test_news_failure_is_harmless(self):
        now = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)

        def broken(*args, **kwargs):
            raise FetchError("down")

        self.assertIsNone(ipo_gmp.enrich_with_news(self.rows(), now, broken)[0]["news_gmp"])

    def test_search_count_is_capped(self):
        now = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
        calls = []
        many = [{"name": f"Firm {i}", "gmp": 1.0, "listing": "2026-11-27"} for i in range(30)]
        ipo_gmp.enrich_with_news(many, now, lambda *a, **k: calls.append(1) or [])
        self.assertEqual(len(calls), ipo_gmp.NEWS_MAX_IPOS)


class FetchTests(unittest.TestCase):
    NOW = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)

    def test_fetch_and_cache(self):
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            return page(row(UPCOMING)).encode()

        rows, ok, stamp, cache = ipo_gmp.fetch_india(self.NOW, None, fetch)
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(calls), 1)
        later = datetime(2026, 11, 14, 8, 20, tzinfo=timezone.utc)
        rows2, ok2, _, _ = ipo_gmp.fetch_india(later, cache, fetch)
        self.assertEqual(len(calls), 1, "a fresh cache must not trigger another request")
        self.assertTrue(ok2)
        self.assertEqual(rows2, rows)
        much_later = datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)
        ipo_gmp.fetch_india(much_later, cache, fetch)
        self.assertEqual(len(calls), 2)

    def test_failure_keeps_old_rows_and_flags_it(self):
        def broken(url, **kwargs):
            raise FetchError("down")

        old = {"ts": 0, "fetched_at": "2026-11-13T08:00:00+00:00", "rows": [{"name": "Old"}]}
        rows, ok, stamp, cache = ipo_gmp.fetch_india(self.NOW, old, broken)
        self.assertFalse(ok)
        self.assertEqual(rows, [{"name": "Old"}])
        self.assertEqual(stamp, "2026-11-13T08:00:00+00:00")

    def test_failure_with_no_cache(self):
        rows, ok, stamp, cache = ipo_gmp.fetch_india(self.NOW, None, lambda url, **kw: b"<html>blocked</html>")
        self.assertEqual((rows, ok, stamp), ([], False, None))


if __name__ == "__main__":
    unittest.main()
