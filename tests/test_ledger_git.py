import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine import ledger, ledger_git

GIT = shutil.which("git")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@unittest.skipUnless(GIT, "git is not installed")
class LedgerGitTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(ledger_git.remove_tree, self.root)
        # A stand-in for the GitHub repository, with a main branch holding "code".
        self.remote = self.root / "remote.git"
        git("init", "--bare", "--quiet", str(self.remote))
        seed = self.root / "seed"
        git("init", "--quiet", str(seed))
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=seed)
        (seed / "app.py").write_text("print('hello')\n", encoding="utf-8")
        git("add", "-A", cwd=seed)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "code", cwd=seed)
        git("push", "--quiet", str(self.remote), "HEAD:refs/heads/main", cwd=seed)
        self.url = str(self.remote)

    def remote_files(self, branch="data"):
        out = git("ls-tree", "-r", "--name-only", branch, cwd=self.remote)
        return out.splitlines()

    def commits(self, branch="data"):
        return int(git("rev-list", "--count", branch, cwd=self.remote))

    def test_the_branch_is_created_on_first_use_and_the_code_branch_is_untouched(self):
        main_before = git("rev-parse", "main", cwd=self.remote)
        work = self.root / "work"
        self.assertFalse(ledger_git.prepare(work, self.url))
        (work / "calls-2026-11.jsonl").write_text('{"id":"a"}\n', encoding="utf-8")
        self.assertTrue(ledger_git.publish(work, "first"))
        self.assertEqual(self.remote_files(), ["calls-2026-11.jsonl"])
        self.assertEqual(git("rev-parse", "main", cwd=self.remote), main_before)
        self.assertEqual(self.remote_files("main"), ["app.py"], "the data branch holds no code and the code branch no data")

    def test_the_second_run_starts_from_what_the_first_pushed(self):
        first = self.root / "first"
        ledger_git.prepare(first, self.url)
        (first / "calls-2026-11.jsonl").write_text('{"id":"a"}\n', encoding="utf-8")
        ledger_git.publish(first, "first")
        second = self.root / "second"
        self.assertTrue(ledger_git.prepare(second, self.url))
        self.assertEqual((second / "calls-2026-11.jsonl").read_text(encoding="utf-8"), '{"id":"a"}\n')

    def test_nothing_new_means_no_commit(self):
        work = self.root / "work"
        ledger_git.prepare(work, self.url)
        (work / "x.jsonl").write_text("1\n", encoding="utf-8")
        ledger_git.publish(work, "one")
        count = self.commits()
        again = self.root / "again"
        ledger_git.prepare(again, self.url)
        self.assertFalse(ledger_git.publish(again, "nothing"))
        self.assertEqual(self.commits(), count)

    def test_commits_are_made_by_the_bot(self):
        work = self.root / "work"
        ledger_git.prepare(work, self.url)
        (work / "x.jsonl").write_text("1\n", encoding="utf-8")
        ledger_git.publish(work, "Ledger update")
        self.assertEqual(git("log", "-1", "--format=%an|%ae|%s", "data", cwd=self.remote), f"{ledger_git.BOT_NAME}|{ledger_git.BOT_EMAIL}|Ledger update")

    def test_a_push_that_loses_a_race_is_rebased_and_succeeds(self):
        mine, theirs = self.root / "mine", self.root / "theirs"
        ledger_git.prepare(mine, self.url)
        (mine / "base.jsonl").write_text("0\n", encoding="utf-8")
        ledger_git.publish(mine, "base")
        ledger_git.prepare(mine, self.url)
        ledger_git.prepare(theirs, self.url)
        (theirs / "theirs.jsonl").write_text("t\n", encoding="utf-8")
        ledger_git.publish(theirs, "theirs first")
        (mine / "mine.jsonl").write_text("m\n", encoding="utf-8")
        self.assertTrue(ledger_git.publish(mine, "mine second"))
        self.assertEqual(sorted(self.remote_files()), ["base.jsonl", "mine.jsonl", "theirs.jsonl"])
        self.assertEqual(self.commits(), 3)

    def test_an_unreachable_remote_is_an_error_that_does_not_leak_the_token(self):
        token = "ghs_SECRETTOKEN1234567890"
        with self.assertRaises(ledger_git.GitError) as caught:
            ledger_git.prepare(self.root / "work", str(self.root / "no-such-repo.git"), token=token)
        text = str(caught.exception)
        self.assertNotIn(token, text)
        import base64
        self.assertNotIn(base64.b64encode(f"x-access-token:{token}".encode()).decode(), text)

    def test_publish_failure_is_reported_not_swallowed(self):
        work = self.root / "work"
        ledger_git.prepare(work, self.url)
        (work / "x.jsonl").write_text("1\n", encoding="utf-8")
        ledger_git.remove_tree(self.remote)
        with self.assertRaises(ledger_git.GitError):
            ledger_git.publish(work, "will fail")

    def test_an_existing_folder_is_replaced_not_merged(self):
        work = self.root / "work"
        work.mkdir()
        (work / "stale.txt").write_text("old", encoding="utf-8")
        ledger_git.prepare(work, self.url)
        self.assertFalse((work / "stale.txt").exists())

    def test_a_full_two_run_cycle_with_the_real_ledger_keeps_history_append_only(self):
        from tests.test_ledger import call, make_bars
        run1 = self.root / "run1"
        ledger_git.prepare(run1, self.url)
        led = ledger.Ledger(run1).load()
        led.add_call(call())
        led.save()
        ledger_git.publish(run1, "Ledger update 1")

        run2 = self.root / "run2"
        ledger_git.prepare(run2, self.url)
        led2 = ledger.Ledger(run2).load()
        self.assertEqual(len(led2.open_calls()), 1)
        led2.resolve_open({"AAA": make_bars([(100, 109, 99, 108)])}, {})
        led2.save()
        ledger_git.publish(run2, "Ledger update 2")

        self.assertEqual(self.commits(), 2)
        self.assertEqual(sorted(self.remote_files()), ["calls-2026-11.jsonl", "outcomes-2026-11.jsonl"])
        old_calls = git("show", "data~1:calls-2026-11.jsonl", cwd=self.remote)
        new_calls = git("show", "data:calls-2026-11.jsonl", cwd=self.remote)
        self.assertEqual(old_calls, new_calls, "the call line was not touched when the outcome arrived")
        outcome = json.loads(git("show", "data:outcomes-2026-11.jsonl", cwd=self.remote))
        self.assertEqual(outcome["outcome"], "target")


if __name__ == "__main__":
    unittest.main()
