import tempfile
import unittest
from pathlib import Path

from tools import survivorship_probe as sp


def write(rows, name="DEAD.csv"):
    folder = Path(tempfile.mkdtemp())
    path = folder / name
    path.write_text("date,open,high,low,close,volume\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return folder, path


class ProbeTests(unittest.TestCase):
    def test_rows_are_read_and_bad_rows_skipped_with_empty_volume_kept_missing(self):
        _, path = write(["2020-01-01,10,11,9,10.5,1000", "2020-01-02,10,11,9,10.5,", "2020-01-03,x,11,9,10.5,5", "2020-01-04,10,8,9,10.5,5", "2020-01-05,0,1,0,1,5", "2020-01-06,10,11,9,10.5,0"])
        bars = sp.read_bars(path)
        self.assertEqual(bars["d"], ["2020-01-01", "2020-01-02", "2020-01-06"])
        self.assertEqual(bars["v"], [1000.0, None, 0.0])

    def test_a_collapsing_stock_is_tested_and_reported(self):
        rows = [f"2020-{1 + i // 28:02d}-{1 + i % 28:02d},{100 - i * 0.2},{101 - i * 0.2},{99 - i * 0.2},{100 - i * 0.2},1000" for i in range(300)]
        _, path = write(rows)
        out = sp.probe(sp.read_bars(path), "US")
        result = out["short"] or out["long"]
        self.assertIsNotNone(result)
        self.assertLess(result["buy_hold_return"], -0.4)               # the stock lost most of its value

    def test_an_empty_folder_says_so_and_tests_nothing(self):
        self.assertEqual(sp.main(["--folder", tempfile.mkdtemp()]), 0)


if __name__ == "__main__":
    unittest.main()
