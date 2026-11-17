import unittest

from tools import update_universe as uu

CSV = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
       "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,X\nBajaj Finance Ltd.,Financial Services,BAJFINANCE,EQ,X\nSun Pharmaceutical Industries Ltd.,Healthcare,SUNPHARMA,EQ,X\n"
       "Odd Co Ltd.,Something New,ODD,EQ,X\nNot Equity Ltd.,Healthcare,NEQ,BE,X\n")


class UniverseUpdateTests(unittest.TestCase):
    def test_parse_maps_industries_to_the_apps_sectors_and_splits_banks(self):
        out = {s: (n, sec) for s, n, sec in uu.parse_nifty(CSV)}
        self.assertEqual(out["HDFCBANK.NS"], ("HDFC Bank", "Banks"))
        self.assertEqual(out["BAJFINANCE.NS"][1], "Financials")
        self.assertEqual(out["SUNPHARMA.NS"][1], "Pharma and Health")
        self.assertEqual(out["ODD.NS"][1], "Other")
        self.assertNotIn("NEQ.NS", out)

    def test_merge_adds_only_missing_stocks_and_never_touches_existing_ones(self):
        stocks = [["HDFCBANK.NS", "HDFC Bank", "IN", "Banks", ["psu"]]]
        merged, added = uu.merge(stocks, uu.parse_nifty(CSV), [("GROWW.NS", "Groww", "Financials")])
        self.assertEqual(added, ["BAJFINANCE.NS", "SUNPHARMA.NS", "ODD.NS", "GROWW.NS"])
        self.assertEqual(merged[0], ["HDFCBANK.NS", "HDFC Bank", "IN", "Banks", ["psu"]])
        again, none = uu.merge(merged, uu.parse_nifty(CSV), [("GROWW.NS", "Groww", "Financials")])
        self.assertEqual(none, [])
        self.assertEqual(len(again), 5)


if __name__ == "__main__":
    unittest.main()
