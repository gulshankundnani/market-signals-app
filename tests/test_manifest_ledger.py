import json
import tempfile
import unittest
from pathlib import Path

from engine import ledger, manifest


def folder(files):
    root = Path(tempfile.mkdtemp())
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(json.dumps(body), encoding="utf-8")
    return root


GOOD = {"data.json": {"build_id": "b1"}, "index.json": [], "state.json": {}, "detail/00.json": {}}


class ManifestTests(unittest.TestCase):
    def test_a_complete_build_verifies(self):
        root = folder(GOOD)
        manifest.write(root, "b1", "t")
        self.assertEqual(manifest.verify(root), [])

    def test_missing_changed_extra_and_foreign_files_are_found(self):
        root = folder(GOOD)
        manifest.write(root, "b1", "t")
        (root / "detail/00.json").write_text('{"x":1}', encoding="utf-8")
        (root / "index.json").unlink()
        (root / "extra.json").write_text("{}", encoding="utf-8")
        text = " | ".join(manifest.verify(root))
        self.assertIn("detail/00.json does not match", text)
        self.assertIn("index.json is listed but missing", text)
        self.assertIn("extra.json is not in the manifest", text)

    def test_data_from_another_build_and_a_missing_manifest_fail(self):
        root = folder(GOOD)
        manifest.write(root, "other", "t")
        self.assertIn("different build", " ".join(manifest.verify(root)))
        self.assertEqual(manifest.verify(folder(GOOD)), ["manifest.json is missing or unreadable"])

    def test_a_truncated_json_file_fails(self):
        root = folder(GOOD)
        (root / "detail/00.json").write_text('{"cut":', encoding="utf-8")
        manifest.write(root, "b1", "t")
        self.assertIn("not valid JSON", " ".join(manifest.verify(root)))


class LedgerTamperTests(unittest.TestCase):
    def test_a_later_line_cannot_rewrite_an_earlier_call(self):
        root = Path(tempfile.mkdtemp())
        first = {"id": "abc", "type": "call", "price": 100, "date": "2026-11"}
        forged = {"id": "abc", "type": "call", "price": 1, "date": "2026-11"}
        (root / "calls-2026-11.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(forged) + "\n", encoding="utf-8")
        loaded = ledger.Ledger(root).load()
        self.assertEqual(loaded.calls["abc"]["price"], 100)
        self.assertEqual(loaded.conflicts, 1)

    def test_an_identical_repeat_is_not_a_conflict(self):
        root = Path(tempfile.mkdtemp())
        line = json.dumps({"id": "abc", "price": 100})
        (root / "calls-2026-11.jsonl").write_text(line + "\n" + line + "\n", encoding="utf-8")
        self.assertEqual(ledger.Ledger(root).load().conflicts, 0)


if __name__ == "__main__":
    unittest.main()
