"""Keeps the track-record ledger on a 'data' branch of the repository.

GitHub cannot run a database, but a branch of plain files in a public repository is a good record: every change is
timestamped by git, history can be inspected by anyone, and the main branch (your code) is never touched.
"""
import base64
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

BRANCH = "data"
BOT_NAME = "Stock Signals bot"
BOT_EMAIL = "stock-signals-bot@users.noreply.github.com"
PUSH_ATTEMPTS = 3


class GitError(Exception):
    pass


def remove_tree(path):
    """Delete a folder, including git's read-only object files (which plain rmtree cannot remove on Windows)."""
    def clear_and_retry(function, target, _error):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=clear_and_retry)
    else:
        shutil.rmtree(path, onerror=clear_and_retry)


def _secrets(token):
    return [s for s in (token, base64.b64encode(f"x-access-token:{token}".encode()).decode() if token else None) if s]


def _run(args, cwd=None, token=None, check=True):
    """Run git. The token travels as an HTTP header (never in a URL) and is scrubbed from any error text."""
    command = ["git"]
    if token:
        header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        command += ["-c", f"http.extraheader=AUTHORIZATION: basic {header}"]
    command += args
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        for secret in _secrets(token):
            message = message.replace(secret, "***")
        raise GitError(f"git {args[0]} failed: {message[:400]}")
    return result


def prepare(directory, remote, token=None, branch=BRANCH):
    """Make `directory` a working copy of the data branch. The branch is created on first use.

    Returns True if the branch already existed. Anything already in `directory` is replaced.
    """
    directory = Path(directory)
    if directory.exists():
        remove_tree(directory)
    probe = _run(["ls-remote", "--heads", remote, branch], token=token, check=False)
    if probe.returncode != 0:
        message = (probe.stderr or "").strip()
        for secret in _secrets(token):
            message = message.replace(secret, "***")
        raise GitError(f"git ls-remote failed: {message[:400]}")
    exists = bool(probe.stdout.strip())
    if exists:
        _run(["clone", "--quiet", "--branch", branch, "--single-branch", remote, str(directory)], token=token)
    else:
        directory.mkdir(parents=True)
        _run(["init", "--quiet"], cwd=directory)
        _run(["symbolic-ref", "HEAD", f"refs/heads/{branch}"], cwd=directory)
        _run(["remote", "add", "origin", remote], cwd=directory)
    _run(["config", "user.name", BOT_NAME], cwd=directory)
    _run(["config", "user.email", BOT_EMAIL], cwd=directory)
    return exists


def publish(directory, message, token=None, branch=BRANCH):
    """Commit whatever changed and push it. Returns True if a commit was pushed, False if there was nothing new.

    If someone else pushed in the meantime the new commit is rebased on top of theirs and pushed again.
    """
    directory = Path(directory)
    _run(["add", "-A"], cwd=directory)
    if _run(["diff", "--cached", "--quiet"], cwd=directory, check=False).returncode == 0:
        return False
    _run(["commit", "--quiet", "-m", message], cwd=directory)
    last_error = None
    for attempt in range(PUSH_ATTEMPTS):
        try:
            _run(["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], cwd=directory, token=token)
            return True
        except GitError as err:
            last_error = err
            _run(["pull", "--quiet", "--rebase", "origin", branch], cwd=directory, token=token, check=False)
    raise last_error
